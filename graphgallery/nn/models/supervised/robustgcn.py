import tensorflow as tf
from tensorflow.keras import Model, Input
from tensorflow.keras.layers import Dropout, Softmax
from tensorflow.keras.optimizers import Adam
from tensorflow.keras import regularizers

from graphgallery.nn.layers import GaussionConvolution_F, GaussionConvolution_D
from graphgallery.nn.models import SupervisedModel
from graphgallery.sequence import FullBatchNodeSequence
from graphgallery.utils.data_utils import normalize_fn, normalize_adj


class RobustGCN(SupervisedModel):
    """
        Implementation of Robust Graph Convolutional Networks (RobustGCN). 
        [Robust Graph Convolutional Networks Against Adversarial Attacks](https://dl.acm.org/doi/10.1145/3292500.3330851)
        Tensorflow 1.x implementation: https://github.com/thumanlab/nrlweb/blob/master/static/assets/download/RGCN.zip

        Arguments:
        ----------
            adj: shape (N, N), `scipy.sparse.csr_matrix` (or `csc_matrix`) if 
                `is_adj_sparse=True`, `np.array` or `np.matrix` if `is_adj_sparse=False`.
                The input `symmetric` adjacency matrix, where `N` is the number 
                of nodes in graph.
            x: shape (N, F), `scipy.sparse.csr_matrix` (or `csc_matrix`) if 
                `is_x_sparse=True`, `np.array` or `np.matrix` if `is_x_sparse=False`.
                The input node feature matrix, where `F` is the dimension of features.
            labels: `np.array` with shape (N,)
                The ground-truth labels for all nodes in graph.
            norm_adj_rate (List of float scalar, optional): 
                The normalize rate for adjacency matrix `adj`. 
                (default: :obj:`[-0.5, -1]`, i.e., two normalized `adj` with rate `-0.5` 
                and `-1.0`, respectively) 
            norm_x_type (String, optional): 
                How to normalize the node feature matrix. See graphgallery.utils.normalize_fn
                (default :obj: `row_wise`)
            device (String, optional): 
                The device where the model is running on. You can specified `CPU` or `GPU` 
                for the model. (default: :obj: `CPU:0`, i.e., the model is running on 
                the 0-th device `CPU`)
            seed (Positive integer, optional): 
                Used in combination with `tf.random.set_seed` & `np.random.seed` & `random.seed`  
                to create a reproducible sequence of tensors across multiple calls. 
                (default :obj: `None`, i.e., using random seed)
            name (String, optional): 
                Specified name for the model. (default: `class.__name__`)

    """

    def __init__(self, adj, x, labels, norm_adj_rate=[-0.5, -1], norm_x_type='row_wise', 
                 device='CPU:0', seed=None, name=None, **kwargs):

        super().__init__(adj, x, labels, device=device, seed=seed, name=name, **kwargs)

        self.norm_adj_rate = norm_adj_rate
        self.norm_x_fn = normalize_fn(norm_x_type)
        self.preprocess(adj, x)

    def preprocess(self, adj, x):
        adj, x = super().preprocess(adj, x)

        if self.norm_adj_rate is not None:
            adj = normalize_adj([adj, adj], self.norm_adj_rate)    # [adj_1, adj_2]

        if self.norm_x_fn is not None:
            x = self.norm_x_fn(x)

        with tf.device(self.device):
            self.tf_x, self.tf_adj = self.to_tensor([x, adj])

    def build(self, hiddens=[64], activations=['relu'], use_bias=False, dropout=0.6, lr=0.01, l2_norm=1e-4, para_kl=5e-4, gamma=1.0, ensure_shape=True):
        
        assert len(hiddens) == len(activations), "The number of hidden units and " \
                                                "activation function should be the same"
        
        with tf.device(self.device):

            x = Input(batch_shape=[None, self.n_features], dtype=self.floatx, name='features')
            adj = [Input(batch_shape=[None, None], dtype=self.floatx, sparse=True, name='adj_matrix_1'),
                   Input(batch_shape=[None, None], dtype=self.floatx, sparse=True, name='adj_matrix_2')]
            index = Input(batch_shape=[None],  dtype=self.intx, name='index')

            h = Dropout(rate=dropout)(x)
            h, KL_divergence = GaussionConvolution_F(hiddens[0], gamma=gamma,
                                                     use_bias=use_bias,
                                                     activation=activations[0],
                                                     kernel_regularizer=regularizers.l2(l2_norm))([h, *adj])

            # additional layers (usually unnecessay)
            for hid, activation in zip(hiddens[1:], activations[1:]):
                h = Dropout(rate=dropout)(h)
                h = GaussionConvolution_D(hid, gamma=gamma, use_bias=use_bias, activation=activation)([h, *adj])

            h = Dropout(rate=dropout)(h)
            h = GaussionConvolution_D(self.n_classes, gamma=gamma, use_bias=use_bias)([h, *adj])
            # To aviod the UserWarning of `tf.gather`, but it causes the shape 
            # of the input data to remain the same
            if ensure_shape:
                h = tf.ensure_shape(h, [self.n_nodes, self.n_classes])
            h = tf.gather(h, index)
            output = Softmax()(h)

            model = Model(inputs=[x, *adj, index], outputs=output)
            model.compile(loss='sparse_categorical_crossentropy', optimizer=Adam(lr=lr), metrics=['accuracy'])
            model.add_loss(para_kl * KL_divergence)

            self.set_model(model)
            self.built = True

    def train_sequence(self, index):
        index = self.to_int(index)
        labels = self.labels[index]
        with tf.device(self.device):
            sequence = FullBatchNodeSequence([self.tf_x, *self.tf_adj, index], labels)
        return sequence

    def predict(self, index):
        super().predict(index)
        index = self.to_int(index)

        with tf.device(self.device):
            index = self.to_tensor(index)
            logit = self.model.predict_on_batch([self.tf_x, *self.tf_adj, index])

        if tf.is_tensor(logit):
            logit = logit.numpy()
        return logit
